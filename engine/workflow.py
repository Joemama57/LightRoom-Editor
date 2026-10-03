"""The full Match Look job, driven through the Lightroom bridge.

    python3 -m engine.workflow match [--strength 1.0] [--skin] [--color-only] [--no-look] [--no-learning] [--out DIR]
    python3 -m engine.workflow nudge --run DIR --photo NAME [--mask subject] Exposure2012=+0.2 Temperature=-150
    python3 -m engine.workflow learn [--run DIR]
    python3 -m engine.workflow calibrate

`match` copies the active photo's look onto the other selected photos,
solves each one's white balance / exposure / tone so it matches the active
photo, then matches the look itself from pixels (contrast, saturation, HSL,
split toning; engine/look.py). It writes report.json, contact_sheet_before.jpg (look pasted, nothing
solved) and contact_sheet.jpg (final) into the run folder.

`nudge` makes a small relative change to one photo from a run, re-renders it
and rebuilds the contact sheet. Claude Code uses it while reviewing.

`learn` and `calibrate` feed the self-learning (engine/learning.py); `match`
also learns automatically from every run.
"""

import argparse
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from . import align, contact_sheet
from . import look as look_stage
from . import skin as skin_model
from .bridge import Bridge, BridgeError
from .guards import MOSTLY_CLIPPED, different_scene, flags_for
from .learning import Learner, light_bucket, same_light
from .colorspace import delta_e_2000, srgb_to_lab
from .measure import Metrics, load_image, measure_file
from .settings import is_raw, split, starting_corrective
from .solver import CORRECTIVE, MIN_SKIN_FRACTION, Options, at_tone_limit, blend, clamp, match_error, propose, wb_limits

RUNS_DIR = Path.home() / ".matchlook" / "runs"
SNAPSHOT_NAME = "Before Match Look"
REVIEW_FLAGS = {"not_converged", "mostly_clipped", "low_neutral_confidence", "different_scene", "tone_limited",
                "look_limited"}
# Per-photo look stage (fallback when a baked reference's original isn't selected).
# Conservative: photos show different things, so only colour, only small moves.
LOOK_TOLERANCE = 0.5
LOOK_LIGHT_TOLERANCE = 0.5  # fraction of the light tolerance to aim for before it
LOOK_ITERATIONS = 4
PER_PHOTO_LOOK_LIMIT = 12.0
SUBTITLE_CHARS = 58  # what fits under one contact-sheet tile
PER_PHOTO_BAND_RATIO = 2.0
# Photos from the reference's own shoot (same camera and file type, taken within
# a few hours) were already evened out by the camera's auto-exposure. Matching
# their brightness to the reference's would follow content instead (a close-up
# of a white car would be darkened), so they keep the reference's exposure and
# tone, with only a small exposure correction.
SAME_SHOOT_HOURS = 3.0
SAME_SHOOT_EV = 0.3
# Grade fit on the reference's original (same pixels, so it can move further).
GRADE_LIGHT_ITERATIONS = 8
GRADE_LOOK_ITERATIONS = 8
GRADE_LIMIT = 80.0
GRADE_SPLIT_LIMIT = 50.0
GRADE_TOLERANCE = 0.3
GRADE_FIT_SIZE = 512  # renders of the original during the fit
GRADE_PAIR_EDGE = 320  # long edge of the pixel-paired comparison
GRADE_LM_ITERATIONS = 6
GRADE_PULL = 3e-3  # pull of the look sliders toward no change, per probe step squared
GRADE_HUE_LIMIT = 40.0
GRADE_HSL_LIMIT = 60.0
# Robust fit: a class (lightness zone x colour family) whose difference stays
# above this many times the median is probably a local edit in the copy (a
# masked sky); it is down-weighted so it can't drag the global sliders.
GRADE_OUTLIER = 3.0
GRADE_PARTIAL_DE = 4.0  # above this after fitting, the grade is only partial
# Suffixes editors and exports add to a file name: "IMG_1964 copy", "DSC1-Edit", ...
EXPORT_SUFFIX = re.compile(r"([ _-]+(copy|edit|edited|export|final)( ?\d+)?|[ _-]\d{1,2})+$", re.IGNORECASE)


def _corrective_settings(sliders):
    # "Custom" tells Lightroom to use our Temp/Tint rather than As Shot/Auto.
    return dict(sliders, WhiteBalance="Custom")


def _apply(bridge, items, warnings, log):
    """Apply settings and record any value Lightroom didn't take."""
    result = bridge.apply_settings(items) or {}
    for miss in result.get("not_taken") or []:
        msg = f"Lightroom didn't take {miss['key']}={miss.get('wanted')!r} on photo {miss['id']} (has {miss.get('got')!r})"
        if msg not in warnings:
            warnings.append(msg)
            log(f"Warning: {msg}")
    return result


def _skin(metrics):
    if metrics.skin_a is None or metrics.skin_fraction < MIN_SKIN_FRACTION:
        return None
    return skin_model.describe(metrics.skin_L, metrics.skin_a, metrics.skin_b)


def latest_unlearned_run(runs_dir, exclude=None):
    """The most recent earlier run whose edits haven't been learned from yet."""
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return None
    for run in sorted((d for d in runs_dir.iterdir() if d.is_dir()), reverse=True):
        if exclude and run.resolve() == Path(exclude).resolve():
            continue
        report = run / "report.json"
        if report.exists():
            try:
                data = json.loads(report.read_text())
            except ValueError:
                continue
            return run if not data.get("learned") else None
    return None


# A run is too unreliable to learn taste from when more than this share of its
# photos was flagged, or its fitted grade stayed this far from the reference:
# edits made to such a run fix the engine's mistakes, they aren't taste.
UNRELIABLE_FLAGGED = 0.5
UNRELIABLE_GRADE_DE = 6.0
UNDONE_SHARE = 0.5  # this share of photos back at their pre-match settings = the run was undone


def unreliable_reason(report):
    photos = report.get("photos") or []
    flagged = sum(1 for p in photos if REVIEW_FLAGS & set(p.get("flags") or []))
    if photos and flagged / len(photos) > UNRELIABLE_FLAGGED:
        return f"{flagged} of {len(photos)} photos were flagged"
    fit = report.get("grade_fit") or {}
    if (fit.get("delta_e_after") or 0) > UNRELIABLE_GRADE_DE:
        return f"the grade fit stayed {fit['delta_e_after']} away from the reference"
    return None


def _look_fingerprint(settings, keys):
    """Short hash of the given creative settings, to tell whether a photo still
    carries the look a run wrote or was put back to its own."""
    import hashlib

    def norm(v):
        return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    blob = json.dumps({k: norm((settings or {}).get(k)) for k in sorted(keys)}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _was_undone(p, settings, report):
    """Put back to how it was before the match (Edit > Undo, or the "Before
    Match Look" snapshot): its light is back at `before` and so is its look.
    A user who edits a photo keeps the run's look, so that isn't an undo."""
    if not p.get("before"):
        return False
    # A slider missing from the photo's settings is at its default, as `before` records it.
    light = {k: settings.get(k, p["before"][k]) for k in CORRECTIVE}
    if not same_light(light, p["before"], p["is_raw"]):
        return False
    if same_light(p["before"], p["matched"], p["is_raw"]):
        return False  # nothing changed in the first place
    keys = report.get("look_keys")
    if not keys or not p.get("before_look"):
        return True  # older report: judge by the light alone
    now = _look_fingerprint(settings, keys)
    return now == p["before_look"] and now != report.get("look_written")


def look_was_removed(report, p, settings):
    """True when the look this run wrote is no longer on the photo: the match
    was undone, a snapshot was restored or the photo was reset. What its
    sliders say now is not an edit of the match, so it isn't learned from."""
    written = {**(report.get("reference", {}).get("creative_look") or {}), **(p.get("look_settings") or {})}
    written = {k: float(v) for k, v in written.items() if isinstance(v, (int, float)) and abs(v) >= 1.0}
    if len(written) < 3:
        return False  # too little of a look to tell
    gone = sum(1 for k, v in written.items() if abs(float(settings.get(k) or 0.0) - v) > 1.0)
    return gone > len(written) / 2


def _mark_learned(run_dir, report, value):
    report["learned"] = value
    (Path(run_dir) / "report.json").write_text(json.dumps(report, indent=2))


def learn_from_run(bridge, run_dir, learner, log=print, backup_to=None):
    """Compare a run's photos as they are in Lightroom now with what the match chose,
    and learn the differences (your edits after the match, and review nudges) as
    preferences. Each run is learned from once.

    Guards against learning the wrong lesson:
    - a photo put back to its pre-match settings (undo, "Before Match Look"
      snapshot) is skipped; if most photos were, the run was undone and
      nothing is learned;
    - a run that went badly (most photos flagged, or a poor grade fit) isn't
      learned from: edits to it correct the engine, they aren't taste.
    The report records why a run was skipped. backup_to: where to back up
    learning.json before changing it."""
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    if report.get("learned"):
        return 0
    reason = unreliable_reason(report)
    if reason:
        _mark_learned(run_dir, report, f"skipped: {reason}")
        log(f"Not learning from {run_dir.name}: {reason}")
        return 0
    try:
        current = {c["id"]: c["settings"] for c in bridge.get_settings([{"id": p["id"]} for p in report["photos"]])}
    except BridgeError as e:
        log(f"Couldn't read last run's photos to learn from them: {e}")
        return 0
    candidates, undone = [], 0
    for p in report["photos"]:
        settings = current.get(p["id"])
        if not settings or "matched" not in p:
            continue
        kept = {k: settings.get(k, p["final"].get(k, 0.0)) for k in CORRECTIVE}
        if _was_undone(p, settings, report) or look_was_removed(report, p, settings):
            undone += 1  # put back as it was before the match: not a preference
            continue
        candidates.append((p, kept))
    total = undone + len(candidates)
    if total and undone / total >= UNDONE_SHARE:
        _mark_learned(run_dir, report, f"skipped: undone ({undone} of {total} photos back to before the match)")
        log(f"Not learning from {run_dir.name}: it was undone")
        return 0
    learned = 0
    for p, kept in candidates:
        bucket = p.get("light") or light_bucket(p["matched"], p["is_raw"])
        if learner.observe_correction(p.get("camera"), p["is_raw"], bucket, p["matched"], kept,
                                      allow_zero=True) == "correction":
            learned += 1
    _mark_learned(run_dir, report, True)
    learner.save(backup_to=backup_to)
    if learned:
        log(f"Learned from {learned} photo(s) adjusted after the last run ({run_dir.name})")
    if undone:
        log(f"Skipped {undone} photo(s) that were put back to how they were before the match")
    return learned


def run_match(
    bridge,
    out_dir,
    strength=1.0,
    tolerance=2.0,
    max_iterations=6,
    size=1024,
    label="yellow",
    skin=False,
    color_only=False,
    learner=None,
    runs_dir=None,
    log=print,
    look=True,
    look_strength=1.0,
    look_per_photo=False,
    original=None,
    grades_dir=None,
    refit=False,
):
    """Match the selected photos to the active one. See the module docstring.

    skin: also keep skin tones consistent (engine/skin.py).
    color_only: solve white balance only; keep each photo's own exposure/tone.
    learner: engine.learning.Learner (self-learning); None disables learning.
    look: when the reference is an exported JPEG with its edit baked in (no
        creative settings to copy), recover the edit by fitting Lightroom
        settings that turn its unedited original into it (fit_grade), then
        copy that grade to every photo. Without the original selected, fall
        back to a small per-photo colour match (engine/look.py).
    look_strength: 0..1, how much of the fitted look to apply.
    look_per_photo: force the per-photo colour match.
    original: file name of the reference's unedited original (default: found
        by name among the selected photos).
    grades_dir: where fitted grades are kept for re-use (None: don't keep).
    refit: fit the grade again even if one was kept for this reference.
    runs_dir: where earlier runs live, to learn from edits made since; default
        is out_dir's parent.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if learner is not None:
        previous = latest_unlearned_run(runs_dir or out_dir.parent, exclude=out_dir)
        if previous:
            learn_from_run(bridge, previous, learner, log, backup_to=out_dir / "learning_before.json")

    selection = bridge.get_selection()
    photos = {p["id"]: p for p in selection["photos"]}
    ref_id = selection.get("active")
    if ref_id not in photos:
        raise ValueError("No active photo. Click the graded photo so it is the active (most-selected) one.")
    targets = [p for pid, p in photos.items() if pid != ref_id]
    if not targets:
        raise ValueError("Select the graded photo plus at least one other photo.")
    ref = photos[ref_id]
    ref_raw = is_raw(ref)
    log(f"Reference: {ref['fileName']}  ·  {len(targets)} photo(s) to match")

    creative, ref_corrective = split(ref["settings"])

    ref_path = out_dir / "reference.jpg"
    bridge.render([{"id": ref_id, "path": str(ref_path)}], size=size)
    ref_metrics = measure_file(ref_path)
    hint = (ref_metrics.a, ref_metrics.b)

    warnings = []
    snap = bridge.snapshot([{"id": t["id"], "name": SNAPSHOT_NAME} for t in targets]) or {}
    if snap.get("warning"):
        warnings.append(snap["warning"])
        log(f"Warning: {snap['warning']}")

    baked = look and look_stage.is_baked(creative)
    grade_fit = None
    shoot_ref = ref  # whose shoot, camera and light the targets are compared with
    if baked:
        source = find_original(ref, targets, original)
        if source:
            log(f"Reference looks exported from another editor; learning its grade from the original {source['fileName']}")
            creative, grade_fit = fit_grade(bridge, ref, source, creative, ref_corrective, ref_metrics, ref_path,
                                            out_dir, size, look_strength, warnings, log,
                                            grades_dir=grades_dir, refit=refit)
            shoot_ref = source
        else:
            look_per_photo = True
            msg = ("Couldn't find the unedited original of the reference among the selected photos; matching colour "
                   "conservatively. Select the original too for an exact match.")
            warnings.append(msg)
            log(msg)
    per_photo_look = look and look_per_photo
    # A fitted grade sets the tone; each photo then only needs its white
    # balance and exposure, matched on neutrals and mid-tones.
    light_only = grade_fit is not None and not color_only

    state = {}
    for t in targets:
        t_raw = is_raw(t)
        camera = t.get("cameraModel")
        start, wb_from_camera = starting_corrective(ref_corrective, ref_raw, t_raw, t.get("settings"))
        shoot = not color_only and same_shoot(shoot_ref, t)
        tone_limits = (SAME_SHOOT_EV, 0.0) if shoot else Options().tone_limits
        if shoot and grade_fit and grade_fit.get("light"):
            # The original's own light, as fitted: the copy's sliders don't carry it.
            for key in CORRECTIVE[2:]:
                start[key] = float(grade_fit["light"].get(key, start[key]))
        if color_only:
            # Keep this photo's own exposure and tone; only white balance is solved.
            for key in CORRECTIVE[2:]:
                start[key] = float((t.get("settings") or {}).get(key, 0.0))
        prior, prior_source = learner.prior(camera, t_raw) if learner is not None else (None, None)
        flags = []
        if camera != ref.get("cameraModel"):
            flags.append("different_camera")
        if t_raw != ref_raw:
            flags.append("different_file_type")
        if shoot:
            flags.append("same_shoot")
        own = t.get("settings") or {}
        state[t["id"]] = {
            "photo": t, "is_raw": t_raw, "camera": camera, "start": start, "sliders": start,
            # As it was before this run, to tell an undo from an edit later.
            "before": {k: float(own.get(k, 0.0 if not (k == "Temperature" and t_raw) else 5500.0))
                       for k in CORRECTIVE},
            "before_look": _look_fingerprint(own, creative),
            "wb_from_camera": wb_from_camera,
            "options": Options(prior=prior, skin=skin, color_only=color_only,
                               wb_limits=wb_limits(t_raw, wb_from_camera), light_only=light_only,
                               tone_limits=tone_limits),
            "same_shoot": shoot,
            "prior_source": prior_source,
            "history": [], "done": False, "extra_flags": flags,
        }

    _apply(
        bridge,
        [{"id": pid, "settings": {**creative, **_corrective_settings(s["start"])}} for pid, s in state.items()],
        warnings,
        log,
    )

    iteration = 0
    while True:
        pending = [pid for pid, s in state.items() if not s["done"]]
        if not pending:
            break
        it_dir = out_dir / f"iter_{iteration}"
        it_dir.mkdir(exist_ok=True)
        items = [{"id": pid, "path": str(it_dir / f"{_safe(state[pid]['photo'])}.jpg")} for pid in pending]
        bridge.render(items, size=size)

        updates = []
        for item in items:
            s = state[item["id"]]
            metrics = measure_file(item["path"], hint)
            s["history"].append({"sliders": s["sliders"], "metrics": metrics.to_dict(), "preview": item["path"]})
            if len(s["history"]) == 1 and different_scene(ref_metrics, metrics) and not s["options"].color_only:
                # A different kind of scene (a dark LED-lit interior against a
                # bright exterior): match its colour, keep its own brightness.
                s["options"] = replace(s["options"], color_only=True)
                s["extra_flags"].append("different_scene")
            # With the look stage on, settle the light more tightly first: a
            # leftover cast would otherwise be "fixed" with HSL instead of white balance.
            p = propose(ref_metrics, s["history"], is_raw=s["is_raw"],
                        tolerance=tolerance * LOOK_LIGHT_TOLERANCE if per_photo_look else tolerance,
                        max_iterations=max_iterations, options=s["options"])
            s["proposal"] = p
            if p.done:
                s["done"] = True
            else:
                s["sliders"] = p.sliders
                updates.append({"id": item["id"], "settings": _corrective_settings(p.sliders)})
        log(f"Pass {iteration + 1}: {len(pending) - len(updates)} done, {len(updates)} still converging")
        if updates:
            _apply(bridge, updates, warnings, log)
        iteration += 1

    # Final values: best render found, scaled by strength, plus anything learned
    # about your taste for this camera and kind of light.
    finals, rerender = [], []
    for pid, s in state.items():
        p, o = s["proposal"], s["options"]
        errors = [match_error(ref_metrics, h["metrics"], skin=o.skin, color_only=o.color_only,
                              light_only=o.light_only) for h in s["history"]]
        best = min(range(len(errors)), key=errors.__getitem__)
        matched = blend(s["start"], p.sliders, strength, s["is_raw"])
        s["light"] = light_bucket(p.sliders, s["is_raw"])
        final, learned = matched, None
        if learner is not None:
            final, learned = learner.apply_preference(matched, s["camera"], s["is_raw"], s["light"])
        s.update(matched=matched, final=final, learned_adjustment=learned, start_error=errors[0])
        finals.append({"id": pid, "settings": _corrective_settings(final)})
        if final == s["history"][best]["sliders"] and best == len(errors) - 1:
            s["preview"] = s["history"][-1]["preview"]
            s["final_metrics"] = Metrics.from_dict(s["history"][-1]["metrics"])
        else:
            rerender.append(pid)
    _apply(bridge, finals, warnings, log)

    if rerender:
        final_dir = out_dir / "final"
        final_dir.mkdir(exist_ok=True)
        items = [{"id": pid, "path": str(final_dir / f"{_safe(state[pid]['photo'])}.jpg")} for pid in rerender]
        bridge.render(items, size=size)
        for item in items:
            state[item["id"]]["preview"] = item["path"]
            state[item["id"]]["final_metrics"] = measure_file(item["path"], hint)

    if per_photo_look:
        _match_look(bridge, state, creative, ref_path, out_dir, size, look_strength, warnings, log, hint)

    for s in state.values():
        o = s["options"]
        s["final_error"] = match_error(ref_metrics, s["final_metrics"], skin=o.skin, color_only=o.color_only,
                                       light_only=o.light_only)
        s["flags"] = flags_for(s["final_metrics"], s["proposal"], tolerance)
        # Only worth a look when the limit kept it from matching. A photo from
        # the reference's shoot is held near its exposure on purpose: what's
        # left is content, not a failed match.
        if s["same_shoot"]:
            # Judge it on colour alone: its brightness differs by content.
            color_error = match_error(ref_metrics, s["final_metrics"], color_only=True)
            if color_error < tolerance and "not_converged" in s["flags"]:
                s["flags"].remove("not_converged")
        elif s["final_error"] >= tolerance and not o.color_only and at_tone_limit(s["start"], s["proposal"].sliders, s["is_raw"], o.tone_limits):
            s["flags"].append("tone_limited")
        unscaled = strength == 1.0 and not s["learned_adjustment"]
        if unscaled and not s["same_shoot"] and s["final_error"] >= tolerance and "not_converged" not in s["flags"]:
            s["flags"].append("not_converged")
        if s.get("look_limited"):
            s["flags"].append("look_limited")

    if learner is not None:
        for s in state.values():
            learner.observe_history(s["camera"], s["is_raw"], s["history"])
        learner.end_run()
        learner.save(backup_to=out_dir / "learning_before.json")

    ref_skin = _skin(ref_metrics)
    report = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strength": strength,
        "tolerance": tolerance,
        "size": size,
        "options": {"skin": skin, "color_only": color_only, "learning": learner is not None,
                    "look": look, "look_strength": look_strength, "look_per_photo": per_photo_look,
                    "light_only": light_only},
        "grade_fit": grade_fit,
        "baked_reference": bool(baked),
        # To recognise an undone run later (see _was_undone).
        "look_keys": sorted(creative),
        "look_written": _look_fingerprint(creative, creative),
        "snapshot": None if snap.get("warning") else SNAPSHOT_NAME,
        "warnings": warnings,
        "reference": {"id": ref_id, "fileName": ref["fileName"], "preview": str(ref_path),
                      "metrics": ref_metrics.to_dict(), "skin": ref_skin,
                      "creative_look": {k: creative[k] for k in look_stage.LOOK_KEYS if k in creative}},
        "photos": [],
    }
    for pid, s in state.items():
        t_skin = _skin(s["final_metrics"])
        report["photos"].append({
            "id": pid,
            "fileName": s["photo"]["fileName"],
            "camera": s["camera"],
            "is_raw": s["is_raw"],
            "light": s["light"],
            "wb_from_camera": s["wb_from_camera"],
            "learned_sensitivities": s["prior_source"],
            "before": s["before"],
            "before_look": s["before_look"],
            "start": s["start"],
            "matched": s["matched"],
            "final": s["final"],
            "learned_adjustment": s["learned_adjustment"],
            "start_error": round(s["start_error"], 2),
            "final_error": round(s["final_error"], 2),
            "iterations": s["proposal"].iterations,
            "look_start_error": s.get("look_start_error"),
            "look_final_error": s.get("look_final_error"),
            "look_settings": s.get("look_settings", {}),
            "look_note": s.get("look_note"),
            "flags": s["flags"] + s["extra_flags"],
            "skin": t_skin,
            "skin_vs_reference": skin_model.compare(ref_skin, t_skin) if ref_skin and t_skin else None,
            "start_preview": s["history"][0]["preview"],
            "preview": s["preview"],
        })
    _label_flagged(bridge, report, label)
    _write_outputs(out_dir, report)
    return report


def _stem(name):
    stem = Path(name or "").stem.strip()
    return EXPORT_SUFFIX.sub("", stem).strip().lower()


def same_shoot(ref, target):
    """True when `target` comes from the same shoot as `ref`: same camera and
    file type, and capture times within SAME_SHOOT_HOURS. Without capture
    times it can't tell, so it says no."""
    a, b = ref.get("captureTime"), target.get("captureTime")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    if not ref.get("cameraModel") or ref.get("cameraModel") != target.get("cameraModel"):
        return False
    return is_raw(ref) == is_raw(target) and abs(a - b) <= SAME_SHOOT_HOURS * 3600


def find_original(ref, photos, name=None):
    """The unedited original of an exported reference among `photos`, or None.

    name: the original's file name or stem, if given explicitly. Otherwise a
    photo whose name is the reference's without an export suffix
    ("IMG_1964 copy.jpg" -> "IMG_1964.JPG"); capture time breaks ties."""
    if name:
        wanted = Path(name).stem.lower()
        found = [p for p in photos if Path(p.get("fileName") or "").stem.lower() == wanted or p.get("fileName") == name]
        if not found:
            raise ValueError(f"--original {name} isn't among the selected photos")
        return found[0]
    stem = _stem(ref.get("fileName"))
    if not stem or stem == Path(ref.get("fileName") or "").stem.lower():
        # The reference's name has no export suffix: it is not a copy of anything.
        same = []
    else:
        same = [p for p in photos if Path(p.get("fileName") or "").stem.lower() == stem]
    if not same:
        same = [p for p in photos if _stem(p.get("fileName")) == stem and p.get("fileName") != ref.get("fileName")]
    if len(same) > 1 and ref.get("captureTime") is not None:
        timed = [p for p in same if p.get("captureTime") == ref.get("captureTime")]
        same = timed or same
    return same[0] if same else None


def pixel_delta_e(path_a, path_b, max_aspect_diff=0.02):
    """Mean CIEDE2000 between two renders of the same picture, pixel by pixel
    (None when they aren't the same framing)."""
    a, b = load_image(path_a), load_image(path_b)
    ra, rb = a.shape[1] / a.shape[0], b.shape[1] / b.shape[0]
    if abs(ra - rb) / ra > max_aspect_diff:
        return None
    if b.shape[:2] != a.shape[:2]:
        b = np.asarray(Image.fromarray((b * 255).round().astype(np.uint8)).resize((a.shape[1], a.shape[0]),
                                                                                   Image.LANCZOS), float) / 255
    la, lb = srgb_to_lab(a[::2, ::2].reshape(-1, 3)), srgb_to_lab(b[::2, ::2].reshape(-1, 3))
    return float(np.mean(delta_e_2000(la, lb)))


def _fit_grade_stats(bridge, ref, original, creative, ref_corrective, ref_metrics, ref_path, out_dir, size,
                     look_strength, warnings, log):
    """Fallback grade fit when the copy can't be lined up with its original:
    compares whole-image statistics instead of pixels.

    Same pixels on both sides, so any difference is the grade: first the light
    (white balance, exposure, tone) as for any photo, then the look sliders
    (contrast, curve, saturation, HSL, split toning). Returns the creative
    settings to copy to every photo, and a summary for the report."""
    fit_dir = out_dir / "grade_fit"
    fit_dir.mkdir(exist_ok=True)
    oid, o_raw = original["id"], is_raw(original)
    hint = (ref_metrics.a, ref_metrics.b)
    start, wb_from_camera = starting_corrective(ref_corrective, is_raw(ref), o_raw, original.get("settings"))
    options = Options(wb_limits=wb_limits(o_raw, wb_from_camera))

    def render(tag, settings=None):
        if settings:
            _apply(bridge, [{"id": oid, "settings": settings}], warnings, log)
        path = fit_dir / f"{tag}.jpg"
        bridge.render([{"id": oid, "path": str(path)}], size=size)
        return path

    # 1. Light.
    sliders, history = start, []
    path = render("light_0", {**creative, **_corrective_settings(start)})
    for i in range(1, GRADE_LIGHT_ITERATIONS + 2):
        history.append({"sliders": sliders, "metrics": measure_file(path, hint).to_dict()})
        p = propose(ref_metrics, history, is_raw=o_raw, tolerance=1.0, max_iterations=GRADE_LIGHT_ITERATIONS,
                    options=options)
        if p.done:
            break
        sliders = p.sliders
        path = render(f"light_{i}", _corrective_settings(sliders))
    light = p.sliders
    lit = render("light_best", _corrective_settings(light))
    delta_before = pixel_delta_e(ref_path, fit_dir / "light_0.jpg")
    log(f"Grade fit: light matched (error {p.best_residual:.1f}); now contrast and colour")

    # 2. Look.
    ref_look = look_stage.measure_look_file(ref_path)
    cur, masks = look_stage.measure_look_file_with_masks(lit)
    plan = look_stage.make_plan(ref_look, cur, tone=True, split=not look_stage.has_split_toning(creative))
    look_history = [{"offsets": {}, "look": cur}]
    for i in range(1, GRADE_LOOK_ITERATIONS + 2):
        q = look_stage.propose_look(ref_look, look_history, plan, tolerance=GRADE_TOLERANCE,
                                    max_iterations=GRADE_LOOK_ITERATIONS, limit=GRADE_LIMIT,
                                    split_limit=GRADE_SPLIT_LIMIT)
        if q.done:
            break
        path = render(f"look_{i}", look_stage.to_settings(q.offsets, creative, GRADE_SPLIT_LIMIT))
        look_history.append({"offsets": q.offsets, "look": look_stage.measure_look_file(path, masks)})
    offsets = {k: float(round(v * look_strength)) for k, v in q.offsets.items()}
    look_settings = look_stage.to_settings(offsets, creative, GRADE_SPLIT_LIMIT)
    fitted = render("fitted", look_settings) if look_settings else lit
    delta_after = pixel_delta_e(ref_path, fitted)
    if delta_after is not None:
        log(f"Grade fit: {delta_before:.1f} -> {delta_after:.1f} ΔE from the reference")
    return {**creative, **look_settings}, {
        "original": original["fileName"],
        "delta_e_before": None if delta_before is None else round(delta_before, 2),
        "delta_e_after": None if delta_after is None else round(delta_after, 2),
        "look_error": round(q.best_error, 2),
        "look_settings": look_settings,
        "note": look_stage.describe(offsets) or None,
        "light": light,
        "preview": str(fitted),
        "aligned": None,
    }


# Variables of the paired grade fit: (name, probe step, low, high). Look
# sliders are offsets on the reference's own creative values.
_GRADE_LOOK_VARS = (
    [(k, 20.0, -GRADE_LIMIT, GRADE_LIMIT) for k in look_stage.TONE_KEYS + look_stage.SAT_KEYS]
)
_SPLIT_VARS = [(k, 15.0, -GRADE_SPLIT_LIMIT, GRADE_SPLIT_LIMIT) for k in look_stage.SPLIT_VARS]


def _grade_variables(o_raw, bands, split):
    light = [("Temperature", 400.0, 2000.0, 50000.0) if o_raw else ("Temperature", 10.0, -100.0, 100.0),
             ("Tint", 10.0, -150.0, 150.0), ("Exposure2012", 0.3, -5.0, 5.0)]
    # Hue moves are the easiest way to over-fit (and the most visible when
    # wrong on other photos), so they get the tightest bound.
    hsl = [(f"{kind}Adjustment{band}", 20.0, -limit, limit)
           for band in bands for kind, limit in (("Hue", GRADE_HUE_LIMIT), ("Saturation", GRADE_HSL_LIMIT),
                                                 ("Luminance", GRADE_HSL_LIMIT))]
    return light + _GRADE_LOOK_VARS + hsl + (_SPLIT_VARS if split else [])


def _cap_split(x, names):
    x = x.copy()
    for name in ("shadow", "highlight"):
        if f"{name}_a" in names:
            i, j = names.index(f"{name}_a"), names.index(f"{name}_b")
            sat = np.hypot(x[i], x[j])
            if sat > GRADE_SPLIT_LIMIT:
                x[[i, j]] *= GRADE_SPLIT_LIMIT / sat
    return x


def _grade_key(ref, original):
    import hashlib
    blob = json.dumps([ref.get("fileName"), ref.get("settings"), original.get("fileName"),
                       original.get("captureTime")], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def fit_grade(bridge, ref, original, creative, ref_corrective, ref_metrics, ref_path, out_dir, size,
              look_strength, warnings, log, grades_dir=None, refit=False):
    """Recover the edit baked into an exported reference from its unedited original.

    1. Line up the copy with the original (it is often a crop): engine/align.py.
    2. Compare them pixel by pixel, grouped by lightness zone and colour family
       (look.paired_residual): same pixels on both sides, so any difference is
       the grade, never content.
    3. Measure how this Lightroom actually responds: one render per slider.
    4. Solve for the sliders (Levenberg-Marquardt), refining the measured
       response after every render.
    Returns the creative settings to copy to every photo, and a summary.
    Falls back to whole-image statistics when the copy can't be lined up.
    """
    cache = Path(grades_dir) / f"{_stem(ref.get('fileName')) or 'reference'}.json" if grades_dir else None
    key = _grade_key(ref, original)
    if cache and cache.exists() and not refit:
        try:
            saved = json.loads(cache.read_text())
            if saved.get("key") == key:
                offsets = {k: float(round(v * look_strength)) for k, v in saved["offsets"].items()}
                look_settings = look_stage.to_settings(offsets, creative, GRADE_SPLIT_LIMIT)
                # Corrections approved in an earlier review (nudge --grade).
                for k, delta in (saved.get("corrections") or {}).items():
                    current = look_settings.get(k, float(creative.get(k) or 0.0))
                    look_settings[k] = float(round(max(-100.0, min(100.0, current + delta * look_strength))))
                log(f"Using the grade learned earlier from {original['fileName']}"
                    + (" with your approved corrections" if saved.get("corrections") else "")
                    + " (--refit to learn it again)")
                return {**creative, **look_settings}, dict(saved["info"], look_settings=look_settings, cached=True)
        except (OSError, ValueError, KeyError):
            pass

    fit_dir = out_dir / "grade_fit"
    fit_dir.mkdir(exist_ok=True)
    oid, o_raw = original["id"], is_raw(original)
    start, _ = starting_corrective(ref_corrective, is_raw(ref), o_raw, original.get("settings"))
    renders = [0]

    def render(tag, settings):
        _apply(bridge, [{"id": oid, "settings": settings}], warnings, log)
        path = fit_dir / f"{tag}.jpg"
        bridge.render([{"id": oid, "path": str(path)}], size=GRADE_FIT_SIZE)
        renders[0] += 1
        return path

    base_path = render("base", {**creative, **_corrective_settings(start)})
    ref_img, base_img = load_image(ref_path), load_image(base_path)
    found = align.locate(base_img, ref_img)
    if found is None:
        ra, rb = ref_img.shape[1] / ref_img.shape[0], base_img.shape[1] / base_img.shape[0]
        if abs(ra - rb) / ra > 0.02:
            log("Grade fit: couldn't line up the reference with its original; comparing whole images instead")
            return _fit_grade_stats(bridge, ref, original, creative, ref_corrective, ref_metrics, ref_path, out_dir,
                                    size, look_strength, warnings, log)
        found = {"box": [0.0, 0.0, 1.0, 1.0], "score": None}
    box = found["box"]
    scale = GRADE_PAIR_EDGE / max(ref_img.shape[:2])
    pair_size = (max(8, int(ref_img.shape[1] * scale)), max(8, int(ref_img.shape[0] * scale)))
    ref_lab = srgb_to_lab(align.crop(ref_img, [0, 0, 1, 1], pair_size).reshape(-1, 3))

    def lab_of(path):
        return srgb_to_lab(align.crop(load_image(path), box, pair_size).reshape(-1, 3))

    base_rgb = align.crop(base_img, box, pair_size).reshape(-1, 3)
    labels, kept, fractions = look_stage.paired_classes(base_rgb, srgb_to_lab(base_rgb),
                                                        shape=(pair_size[1], pair_size[0]))
    bands = [b for b, f in look_stage.band_fractions(labels, kept, fractions).items() if f >= 0.01]
    variables = _grade_variables(o_raw, bands, split=not look_stage.has_split_toning(creative))
    names = [v[0] for v in variables]
    steps = np.array([v[1] for v in variables])
    lo, hi = np.array([v[2] for v in variables]), np.array([v[3] for v in variables])
    n_light = 3

    def settings_for(x):
        corr = dict(start, **{names[i]: float(x[i]) for i in range(n_light)})
        offsets = {names[i]: float(x[i]) for i in range(n_light, len(names))}
        return {**creative, **_corrective_settings(clamp(corr, o_raw)),
                **look_stage.to_settings(offsets, creative, GRADE_SPLIT_LIMIT)}

    def residual(path):
        return look_stage.paired_residual(lab_of(path), ref_lab, labels, kept, fractions)

    def error(r):
        return look_stage.paired_error(r, fractions)

    x = np.array([float(start.get(n, 0.0)) if i < n_light else 0.0 for i, n in enumerate(names)])
    r = residual(base_path)
    delta_before = _paired_delta_e(lab_of(base_path), ref_lab)
    log(f"Grade fit: lined up with {original['fileName']}"
        + (f" (match {found['score']:.2f})" if found["score"] is not None else "")
        + f"; measuring how {len(names)} sliders respond")

    # Measured response: one probe render per slider.
    J = np.zeros((len(r), len(names)))
    for j in range(len(names)):
        step = steps[j] if x[j] + steps[j] <= hi[j] else -steps[j]
        probe = x.copy()
        probe[j] += step
        J[:, j] = (residual(render(f"probe_{names[j]}", settings_for(probe))) - r) / step

    # Levenberg-Marquardt in probe-step units, with a pull toward no change and
    # robust (Huber-style) weights recomputed from the best fit so far.
    classes = look_stage.paired_class_fractions(kept, fractions)

    def robust_weights(res):
        norms = _class_norms(res, classes)
        cut = max(GRADE_OUTLIER * float(np.median(norms)), 2.0)
        return np.repeat(np.minimum(1.0, cut / np.maximum(norms, 1e-9)), 3)

    def werror(res, w):
        return error(np.sqrt(w) * res)

    best_x, best_r = x, r
    lam = 1e-2
    pull = np.array([0.0] * n_light + [GRADE_PULL] * (len(names) - n_light))
    for it in range(GRADE_LM_ITERATIONS):
        w = robust_weights(best_r)
        err = werror(best_r, w)
        Ju = (J * steps) * np.sqrt(w)[:, None]
        A = Ju.T @ Ju
        u = (best_x - np.array([x[i] if i < n_light else 0.0 for i in range(len(names))])) / steps
        lhs = A + lam * np.diag(np.diag(A) + 1e-9) + np.diag(pull)
        du = np.linalg.solve(lhs, -(Ju.T @ (np.sqrt(w) * best_r)) - pull * u)
        cand = _cap_split(np.clip(best_x + du * steps, lo, hi), names)
        if np.abs(cand - best_x).max() < 1e-6:
            break
        cr = residual(render(f"fit_{it}", settings_for(cand)))
        dx = cand - best_x
        J = J + np.outer(cr - best_r - J @ dx, dx) / (dx @ dx)  # Broyden: refine the measured response
        cerr = werror(cr, w)
        if cerr < err:
            improvement = (err - cerr) / err
            best_x, best_r = cand, cr
            lam = max(lam / 3, 1e-4)
            if improvement < 0.01:
                break
        else:
            lam *= 4

    offsets = {names[i]: float(round(best_x[i])) for i in range(n_light, len(names))}
    scaled = {k: float(round(v * look_strength)) for k, v in offsets.items()}
    look_settings = look_stage.to_settings(scaled, creative, GRADE_SPLIT_LIMIT)
    final_x = best_x.copy()
    for i in range(n_light, len(names)):
        final_x[i] = scaled[names[i]]
    fitted = render("fitted", settings_for(final_x))
    delta_after = _paired_delta_e(lab_of(fitted), ref_lab)
    log(f"Grade fit: {delta_before:.1f} -> {delta_after:.1f} difference from the reference ({renders[0]} renders)")
    light = clamp(dict(start, **{names[i]: float(best_x[i]) for i in range(n_light)}), o_raw)
    err = error(best_r)
    at_limits = [names[i] for i in range(n_light, len(names)) if abs(best_x[i]) >= hi[i] - 0.5]
    unmatched = _unmatched_regions(lab_of(fitted), ref_lab, labels, kept)
    if delta_after > GRADE_PARTIAL_DE:
        msg = (f"The grade only partly matches the reference (off by {delta_after:.1f}): the copy probably has "
               "local edits" + (f" (around the {', '.join(unmatched)} of the picture)" if unmatched else "")
               + " that sliders can't copy")
        warnings.append(msg)
        log(msg)
    info = {
        "original": original["fileName"],
        "aligned": found,
        "delta_e_before": round(delta_before, 2),
        "delta_e_after": round(delta_after, 2),
        "look_error": round(err, 2),
        "note": look_stage.describe(scaled) or None,
        "at_limits": at_limits,
        "unmatched": unmatched,
        "light": light,
        "renders": renders[0],
        "preview": str(fitted),
    }
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "offsets": offsets, "info": info}, indent=1))
    return {**creative, **look_settings}, dict(info, look_settings=look_settings)




def _class_norms(res, classes):
    """Mean colour difference per class (undoing the size weighting)."""
    r = res.reshape(-1, 3)
    return np.array([np.linalg.norm(r[i]) / np.sqrt(f) for i, f in enumerate(classes.values())])


def _unmatched_regions(fitted_lab, ref_lab, labels, kept, limit=4):
    """Areas of the picture that still differ a lot after the fit: most likely
    edited locally in the copy (a brushed or masked adjustment)."""
    tiles = look_stage.PAIR_TILES ** 2
    diff = {k: float(np.mean(delta_e_2000(fitted_lab[labels == k], ref_lab[labels == k]))) for k in kept}
    if not diff:
        return []
    cut = max(2.5 * float(np.median(list(diff.values()))), 6.0)
    by_tile = {}
    for k, d in diff.items():
        if d > cut:
            by_tile[k % tiles] = by_tile.get(k % tiles, 0) + 1
    return [_TILE_NAMES[t] if look_stage.PAIR_TILES == 3 else f"tile {t}"
            for t, _ in sorted(by_tile.items(), key=lambda tc: -tc[1])][:limit]


_TILE_NAMES = ["top left", "top", "top right", "left", "centre", "right", "bottom left", "bottom", "bottom right"]



def _paired_delta_e(lab_a, lab_b):
    return float(np.mean(delta_e_2000(lab_a, lab_b)))


def _match_look(bridge, state, creative, ref_path, out_dir, size, look_strength, warnings, log, hint):
    """Per-photo colour match (fallback): small creative offsets per photo
    toward the reference's colour families (engine/look.py). Photos show
    different things, so it stays conservative: no tone (the light stage owns
    it), no split toning, only bands both photos share in similar amounts."""
    ref_look = look_stage.measure_look_file(ref_path)
    for s in state.values():
        cur, s["look_masks"] = look_stage.measure_look_file_with_masks(s["preview"])
        # A blown-out frame has nothing to match; a different kind of scene
        # keeps its own brightness, so only its colour is refined.
        skip = s["final_metrics"].clipped_fraction > MOSTLY_CLIPPED
        s["look_plan"] = (look_stage.Plan(tone=False, enabled=False) if skip else
                          look_stage.make_plan(ref_look, cur, tone=False, split=False, skin=s["options"].skin,
                                               max_ratio=PER_PHOTO_BAND_RATIO))
        s["look_history"] = [{"offsets": {}, "look": cur, "preview": s["preview"],
                              "metrics": s["final_metrics"]}]
        s["look_done"] = False

    iteration = 0
    while True:
        updates = []
        for pid, s in state.items():
            if s["look_done"]:
                continue
            p = look_stage.propose_look(ref_look, s["look_history"], s["look_plan"], tolerance=LOOK_TOLERANCE,
                                        max_iterations=LOOK_ITERATIONS, limit=PER_PHOTO_LOOK_LIMIT)
            s["look_proposal"] = p
            if p.done:
                s["look_done"] = True
            else:
                s["look_offsets"] = p.offsets
                updates.append({"id": pid, "settings": look_stage.to_settings(p.offsets, creative)})
        if not updates:
            break
        log(f"Look pass {iteration + 1}: {len(updates)} photo(s) refining colour and contrast")
        _apply(bridge, updates, warnings, log)
        it_dir = out_dir / f"look_{iteration}"
        it_dir.mkdir(exist_ok=True)
        items = [{"id": u["id"], "path": str(it_dir / f"{_safe(state[u['id']]['photo'])}.jpg")} for u in updates]
        bridge.render(items, size=size)
        for item in items:
            s = state[item["id"]]
            s["look_history"].append({"offsets": s["look_offsets"],
                                      "look": look_stage.measure_look_file(item["path"], s["look_masks"]),
                                      "preview": item["path"], "metrics": measure_file(item["path"], hint)})
        iteration += 1

    finals, rerender = [], []
    for pid, s in state.items():
        p = s["look_proposal"]
        offsets = {k: float(round(v * look_strength)) for k, v in p.offsets.items()}
        settings = look_stage.to_settings(offsets, creative)
        s["look_settings"] = settings
        s["look_note"] = look_stage.describe(offsets) or None
        s["look_limited"] = p.limited
        s["look_start_error"] = round(look_stage.look_error(ref_look, s["look_history"][0]["look"], s["look_plan"]), 2)
        best = next((h for h in s["look_history"] if _same_offsets(h["offsets"], offsets)), None)
        if settings:
            finals.append({"id": pid, "settings": settings})
        if best is not None:
            s["preview"], s["final_metrics"] = best["preview"], best["metrics"]
            s["look_final_error"] = round(look_stage.look_error(ref_look, best["look"], s["look_plan"]), 2)
        else:
            rerender.append(pid)
    if finals:
        _apply(bridge, finals, warnings, log)
    if rerender:
        final_dir = out_dir / "look_final"
        final_dir.mkdir(exist_ok=True)
        items = [{"id": pid, "path": str(final_dir / f"{_safe(state[pid]['photo'])}.jpg")} for pid in rerender]
        bridge.render(items, size=size)
        for item in items:
            s = state[item["id"]]
            s["preview"], s["final_metrics"] = item["path"], measure_file(item["path"], hint)
            s["look_final_error"] = round(look_stage.look_error(ref_look, look_stage.measure_look_file(item["path"], s["look_masks"]),
                                                                s["look_plan"]), 2)


def _same_offsets(a, b):
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) < 0.05 for k in set(a) | set(b))


def _label_flagged(bridge, report, label):
    if not label:
        return
    items = [{"id": p["id"], "label": label} for p in report["photos"] if REVIEW_FLAGS & set(p["flags"])]
    if items:
        bridge.set_label(items)


def _safe(photo):
    stem = Path(photo.get("fileName") or photo["id"]).stem
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in stem) + f"_{photo['id']}"


def _tiles(report, key):
    tiles = [{"path": report["reference"]["preview"], "title": f"REFERENCE · {report['reference']['fileName']}",
              "subtitle": "the look to match"}]
    fit = report.get("grade_fit")
    if fit and fit.get("delta_e_after") is not None and key == "preview":
        tiles[0]["subtitle"] = f"grade learned from {fit['original']} (off by {fit['delta_e_after']:.1f})"
    for p in report["photos"]:
        err = p["start_error"] if key == "start_preview" else p["final_error"]
        flags = [f for f in p["flags"] if key == "preview" or f not in REVIEW_FLAGS]
        notes = list(flags)
        skin_on = report.get("options", {}).get("skin")
        if key == "preview" and skin_on and p.get("skin_vs_reference") and p["skin_vs_reference"] != "matches the reference":
            notes.append(f"skin {p['skin_vs_reference']}")
        if key == "preview" and p.get("learned_adjustment"):
            notes.append("learned adj.")
        look_err = p.get("look_start_error") if key == "start_preview" else p.get("look_final_error")
        if look_err is not None:
            notes.insert(0, f"look {look_err:.1f}")
        tiles.append({
            "path": p[key],
            "title": p["fileName"],
            "subtitle": _subtitle(f"error {err:.1f}", notes),
            "highlight": key == "preview" and bool(REVIEW_FLAGS & set(p["flags"])),
        })
    return tiles


def _subtitle(head, notes, width=SUBTITLE_CHARS):
    """'error 1.2 · flag, flag' cut to fit one tile, with '+N more'."""
    text = head
    for i, note in enumerate(notes):
        more = len(notes) - i - 1
        candidate = f"{text}{' · ' if text == head else ', '}{note}"
        if len(candidate) + (len(f" +{more} more") if more else 0) > width:
            return f"{text} +{len(notes) - i} more"
        text = candidate
    return text


def _write_outputs(out_dir, report):
    out_dir = Path(out_dir)
    contact_sheet.build(_tiles(report, "start_preview"), out_dir / "contact_sheet_before.jpg")
    contact_sheet.build(_tiles(report, "preview"), out_dir / "contact_sheet.jpg")
    report["contact_sheet"] = str(out_dir / "contact_sheet.jpg")
    report["contact_sheet_before"] = str(out_dir / "contact_sheet_before.jpg")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))


def parse_changes(pairs, allow_mask=False):
    """["Exposure2012=+0.2", "Tint=-3"] -> {"Exposure2012": 0.2, "Tint": -3.0}"""
    allowed = list(MASK_SLIDERS) if allow_mask else CORRECTIVE + NUDGE_LOOK_KEYS
    changes = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if key not in allowed:
            raise ValueError(f"{key} is not one of {', '.join(allowed)}")
        changes[key] = float(value)
    return changes


# Global slider name -> the matching local slider inside an AI mask, and its UI range.
MASK_SLIDERS = {
    "Temperature": ("LocalTemperature", 100.0),
    "Tint": ("LocalTint", 100.0),
    "Exposure2012": ("LocalExposure2012", 4.0),
    "Shadows2012": ("LocalShadows2012", 100.0),
    "Highlights2012": ("LocalHighlights2012", 100.0),
    "Whites2012": ("LocalWhites2012", 100.0),
    "Blacks2012": ("LocalBlacks2012", 100.0),
    "Saturation": ("LocalSaturation", 100.0),
}
MASK_KINDS = ("subject", "sky", "background", "people")
# Look sliders a whole-photo nudge may change (split toning is solved, not nudged).
NUDGE_LOOK_KEYS = look_stage.TONE_KEYS + look_stage.SAT_KEYS + look_stage.BAND_KEYS


def _find_photo(report, photo):
    matches = [p for p in report["photos"] if photo in (p["id"], p["fileName"], Path(p["fileName"]).stem)]
    if len(matches) != 1:
        raise ValueError(f"'{photo}' matches {len(matches)} photos in this run")
    return matches[0]


def _rerender(bridge, run_dir, report, p, tag):
    nudge_dir = run_dir / "nudges"
    nudge_dir.mkdir(exist_ok=True)
    n = len(list(nudge_dir.glob("*.jpg")))
    path = nudge_dir / f"{n:02d}_{tag}_{_safe(p)}.jpg"
    bridge.render([{"id": p["id"], "path": str(path)}], size=report.get("size", 1024))
    ref_metrics = Metrics.from_dict(report["reference"]["metrics"])
    metrics = measure_file(path, (ref_metrics.a, ref_metrics.b))
    opts = report.get("options", {})
    p["preview"] = str(path)
    p["final_error"] = round(match_error(ref_metrics, metrics, skin=opts.get("skin", False),
                                         color_only=opts.get("color_only", False),
                                         light_only=opts.get("light_only", False)), 2)
    p["skin"] = _skin(metrics)
    ref_skin = report["reference"].get("skin")
    p["skin_vs_reference"] = skin_model.compare(ref_skin, p["skin"]) if ref_skin and p["skin"] else None
    # A reviewed photo no longer needs the automatic "didn't converge" flag.
    p["flags"] = [f for f in p["flags"] if f != "not_converged"]


def _nudge_one(bridge, run_dir, report, p, changes, mask):
    warnings = report.setdefault("warnings", [])

    if mask is None:
        sliders = dict(p["final"])
        look_settings = dict(p.get("look_settings") or {})
        base = report["reference"].get("creative_look") or {}
        for key, delta in changes.items():
            if key in NUDGE_LOOK_KEYS:
                current = look_settings.get(key, base.get(key, 0.0))
                look_settings[key] = round(max(-100.0, min(100.0, float(current) + delta)), 1)
            elif key in CORRECTIVE:
                sliders[key] = sliders.get(key, 0.0) + delta
            else:
                raise ValueError(f"{key} can only be changed inside a mask (--mask)")
        sliders = clamp(sliders, p["is_raw"])
        changed_look = {k: v for k, v in look_settings.items() if k in changes}
        _apply(bridge, [{"id": p["id"], "settings": {**_corrective_settings(sliders), **changed_look}}],
               warnings, lambda m: None)
        p["final"] = sliders
        p["look_settings"] = look_settings
        tag = "photo"
    else:
        if mask not in MASK_KINDS:
            raise ValueError(f"--mask must be one of {', '.join(MASK_KINDS)}")
        local = dict(p.setdefault("masks", {}).get(mask, {}))
        for key, delta in changes.items():
            local_key, limit = MASK_SLIDERS[key]
            local[local_key] = max(-limit, min(limit, local.get(local_key, 0.0) + delta))
        result = bridge.mask_adjust(p["id"], mask, local)
        if not result.get("found"):
            raise BridgeError(f"Lightroom didn't keep the {mask} mask on {p['fileName']}")
        p["masks"][mask] = local
        tag = mask

    p.setdefault("nudges", []).append({"mask": mask, **changes} if mask else changes)
    _rerender(bridge, run_dir, report, p, tag)

    if mask is not None:
        # Lightroom can accept a mask edit and drop it when it next recomputes the
        # photo (seen in par4987/lightroom-mcp); the render above forced that, so check.
        (current,) = bridge.get_settings([{"id": p["id"]}])
        kept = [c for c in current["settings"].get("MaskGroupBasedCorrections") or []
                if isinstance(c, dict) and c.get("CorrectionName") == f"Match Look {mask}"]
        if not kept:
            msg = f"Lightroom dropped the {mask} mask edit on {p['fileName']} after rendering"
            warnings.append(msg)
            p["masks"].pop(mask, None)

    return p


def run_nudge(bridge, run_dir, photo, changes, mask=None, grade=False, grades_dir=None):
    """Apply relative slider changes to photos from a run and refresh their previews.

    photo: a file name, stem or id, or "all" for every photo in the run.
    mask: None for the whole photo, or "subject" / "sky" / "background" /
    "people" to make the change only inside an AI mask (experimental; creates
    the mask the first time). The change is learned from at the next run.
    grade: the change is a correction to the run's shared grade (look sliders
    only): it goes to every photo, into the report's grade, and into the
    grade kept for this reference (grades_dir), so later runs start from it.
    Returns the changed photo, or the list of them for "all".
    """
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    if grade:
        if mask is not None:
            raise ValueError("--grade changes the whole grade; it can't be used with --mask")
        not_look = [k for k in changes if k not in NUDGE_LOOK_KEYS]
        if not_look:
            raise ValueError(f"--grade only takes look sliders, not {', '.join(not_look)}")
        photo = "all"
    photos = report["photos"] if photo == "all" else [_find_photo(report, photo)]
    done = [_nudge_one(bridge, run_dir, report, p, changes, mask) for p in photos]
    if grade:
        _correct_grade(report, changes, grades_dir)
    _write_outputs(run_dir, report)
    return done if photo == "all" else done[0]


def _correct_grade(report, changes, grades_dir):
    """Fold an approved correction into the run's grade and the kept grade."""
    base = report["reference"].setdefault("creative_look", {})
    for key, delta in changes.items():
        base[key] = round(max(-100.0, min(100.0, float(base.get(key) or 0.0) + delta)), 1)
    fit = report.get("grade_fit")
    if fit:
        for key, delta in changes.items():
            fit.setdefault("look_settings", {})[key] = base[key]
        fit.setdefault("corrections", []).append(dict(changes, date=time.strftime("%Y-%m-%d")))
    if grades_dir:
        cache = Path(grades_dir) / f"{_stem(report['reference'].get('fileName')) or 'reference'}.json"
        if cache.exists():
            saved = json.loads(cache.read_text())
            totals = saved.setdefault("corrections", {})
            for key, delta in changes.items():
                totals[key] = round(totals.get(key, 0.0) + delta, 1)
            saved.setdefault("correction_history", []).append(dict(changes, date=time.strftime("%Y-%m-%d")))
            cache.write_text(json.dumps(saved, indent=1))


# Calibration: one deliberate step per slider, to seed the self-learning before
# the first real run. (Kelvin for raw; offset units for JPEG.)
CALIBRATION_STEPS = {
    "Temperature": (700.0, 15.0),
    "Tint": (12.0, 12.0),
    "Exposure2012": (0.7, 0.7),
    "Shadows2012": (40.0, 40.0),
    "Highlights2012": (40.0, 40.0),
    "Whites2012": (40.0, 40.0),
    "Blacks2012": (40.0, 40.0),
}
CALIBRATION_SNAPSHOT = "Before Match Look calibration"


def run_calibrate(bridge, out_dir, learner, size=768, log=print):
    """Measure how Lightroom's sliders move the measurements on the selected photos,
    feed that to the self-learning, and put every photo back as it was."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    photos = bridge.get_selection()["photos"]
    if not photos:
        raise ValueError("Select one or more photos to calibrate on (varied light is best).")
    bridge.snapshot([{"id": ph["id"], "name": CALIBRATION_SNAPSHOT} for ph in photos])
    original = {ph["id"]: ph["settings"] for ph in photos}
    base = {}
    for ph in photos:
        raw = is_raw(ph)
        st = ph["settings"]
        sliders = {k: float(st.get(k, 0.0)) for k in CORRECTIVE}
        if raw and not sliders["Temperature"]:
            sliders["Temperature"] = 5500.0
        base[ph["id"]] = sliders

    def render_all(tag):
        items = [{"id": ph["id"], "path": str(out_dir / f"{tag}_{_safe(ph)}.jpg")} for ph in photos]
        bridge.render(items, size=size)
        return {i["id"]: measure_file(i["path"]).to_dict() for i in items}

    warnings = []
    _apply(bridge, [{"id": pid, "settings": _corrective_settings(sl)} for pid, sl in base.items()], warnings, log)
    base_metrics = render_all("base")
    pairs = 0
    try:
        for key, (raw_step, jpeg_step) in CALIBRATION_STEPS.items():
            log(f"Calibrating {key}")
            stepped = {}
            for ph in photos:
                sl = dict(base[ph["id"]])
                step = raw_step if is_raw(ph) else jpeg_step
                hi = 50000.0 if (key == "Temperature" and is_raw(ph)) else 5.0 if key == "Exposure2012" else 100.0
                # Step down instead of up when up would leave the slider's range.
                sl[key] = sl[key] + step if sl[key] + step <= hi else sl[key] - step
                stepped[ph["id"]] = sl
            _apply(bridge, [{"id": pid, "settings": _corrective_settings(sl)} for pid, sl in stepped.items()],
                   warnings, log)
            metrics = render_all(key)
            for ph in photos:
                history = [{"sliders": base[ph["id"]], "metrics": base_metrics[ph["id"]]},
                           {"sliders": stepped[ph["id"]], "metrics": metrics[ph["id"]]}]
                pairs += learner.observe_history(ph.get("cameraModel"), is_raw(ph), history)
            _apply(bridge, [{"id": pid, "settings": _corrective_settings(sl)} for pid, sl in base.items()],
                   warnings, log)
    finally:
        # Put every photo back exactly: corrective sliders and white balance mode.
        restore = []
        for pid, st in original.items():
            keep = {k: st[k] for k in CORRECTIVE + ["WhiteBalance"] if k in st}
            restore.append({"id": pid, "settings": keep})
        _apply(bridge, restore, warnings, log)
    learner.end_run()
    learner.save(backup_to=out_dir / "learning_before.json")
    return {"photos": len(photos), "observations": pairs, "warnings": warnings,
            "learning": learner.summary()["sensitivity_samples"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match the look of the active Lightroom photo across the selection.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="match the selected photos to the active one")
    m.add_argument("--strength", type=float, default=1.0, help="0..1, how much of the correction to apply")
    m.add_argument("--skin", action="store_true", help="also keep skin tones consistent (portraits)")
    m.add_argument("--color-only", action="store_true",
                   help="match white balance only; keep each photo's own exposure and tone")
    m.add_argument("--no-learning", action="store_true", help="don't use or update what has been learned")
    m.add_argument("--no-look", action="store_true",
                   help="match light only (white balance, exposure, tone); skip matching colour and contrast")
    m.add_argument("--look-strength", type=float, default=1.0, help="0..1, how much of the look matching to apply")
    m.add_argument("--look-per-photo", action="store_true",
                   help="also nudge each photo's colours toward the reference's (small, per photo)")
    m.add_argument("--original", help="file name of the reference's unedited original, if not found by name")
    m.add_argument("--refit", action="store_true", help="learn an exported reference's grade again")
    m.add_argument("--tolerance", type=float, default=2.0)
    m.add_argument("--max-iterations", type=int, default=6)
    m.add_argument("--size", type=int, default=1024, help="preview long edge in pixels")
    m.add_argument("--label", default="yellow", help="color label for photos that need review ('' for none)")
    m.add_argument("--out", help="run folder (default ~/.matchlook/runs/<time>)")
    n = sub.add_parser("nudge", help="relative slider changes to one photo from a run")
    n.add_argument("--run", required=True, help="run folder from `match`")
    n.add_argument("--photo", required=True, help="file name, file stem or id, or 'all'")
    n.add_argument("--grade", action="store_true",
                   help="a correction to the shared grade: every photo, and kept for the next run")
    n.add_argument("--mask", choices=MASK_KINDS, help="experimental: change only inside this AI mask")
    n.add_argument("changes", nargs="+", help="e.g. Exposure2012=+0.2 Temperature=-150 Tint=+3")
    lr = sub.add_parser("learn", help="learn now from edits made to a run's photos in Lightroom")
    lr.add_argument("--run", help="run folder (default: the latest one not learned from yet)")
    lr.add_argument("--skip", action="store_true", help="don't learn from that run at all (a bad run)")
    c = sub.add_parser("calibrate", help="measure Lightroom's slider response on the selected photos")
    c.add_argument("--out", help="folder for calibration renders")
    args = parser.parse_args(argv)

    bridge = Bridge()
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    try:
        if args.cmd == "match":
            if not 0.0 <= args.strength <= 1.0:
                parser.error("--strength must be between 0 and 1")
            if not 0.0 <= args.look_strength <= 1.0:
                parser.error("--look-strength must be between 0 and 1")
            out = Path(args.out) if args.out else RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
            learner = None if args.no_learning else Learner()
            report = run_match(bridge, out, strength=args.strength, tolerance=args.tolerance,
                               max_iterations=args.max_iterations, size=args.size, label=args.label,
                               skin=args.skin, color_only=args.color_only, learner=learner,
                               runs_dir=RUNS_DIR if not args.out else None, log=log,
                               look=not args.no_look, look_strength=args.look_strength,
                               look_per_photo=args.look_per_photo, original=args.original,
                               grades_dir=RUNS_DIR.parent / "grades", refit=args.refit)
            print(json.dumps({"run": str(out), **_summary(report)}, indent=2))
        elif args.cmd == "nudge":
            done = run_nudge(bridge, args.run, args.photo,
                             parse_changes(args.changes, allow_mask=bool(args.mask)), mask=args.mask,
                             grade=args.grade, grades_dir=RUNS_DIR.parent / "grades")
            keys = ("fileName", "final", "look_settings", "masks", "final_error", "flags", "preview")
            print(json.dumps([{k: p.get(k) for k in keys} for p in (done if isinstance(done, list) else [done])],
                             indent=2))
        elif args.cmd == "learn":
            learner = Learner()
            run = Path(args.run) if args.run else latest_unlearned_run(RUNS_DIR)
            if not run:
                print("Nothing new to learn from.")
            elif args.skip:
                report = json.loads((run / "report.json").read_text())
                _mark_learned(run, report, "skipped: by user")
                print(json.dumps({"run": str(run), "learned": report["learned"]}, indent=2))
            else:
                count = learn_from_run(bridge, run, learner, log, backup_to=run / "learning_before.json")
                print(json.dumps({"run": str(run), "photos_learned_from": count, **learner.summary()}, indent=2))
        else:
            out = Path(args.out) if args.out else RUNS_DIR.parent / "calibration" / time.strftime("%Y%m%d-%H%M%S")
            print(json.dumps(run_calibrate(bridge, out, Learner(), log=log), indent=2))
    except (BridgeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _summary(report):
    # Skin notes only when asked for: on photos without people, orange paint
    # or a brick wall reads as "skin" and the notes are noise.
    skin_on = report.get("options", {}).get("skin")
    return {
        "reference": report["reference"]["fileName"],
        "baked_reference": report.get("baked_reference"),
        "grade_fit": {k: v for k, v in (report.get("grade_fit") or {}).items() if k not in ("look_settings", "light")}
        or None,
        "reference_skin": report["reference"].get("skin") if skin_on else None,
        "contact_sheet": report["contact_sheet"],
        "contact_sheet_before": report["contact_sheet_before"],
        "warnings": report["warnings"],
        "photos": [
            {k: p.get(k) for k in ("fileName", "start_error", "final_error", "look_start_error", "look_final_error",
                                   "look_note", "iterations", "flags", "final", "learned_adjustment")
             + (("skin_vs_reference",) if skin_on else ())}
            for p in report["photos"]
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
