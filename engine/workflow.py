"""The full Match Look job, driven through the Lightroom bridge.

    python3 -m engine.workflow match [--strength 1.0] [--skin] [--color-only] [--no-learning] [--out DIR]
    python3 -m engine.workflow nudge --run DIR --photo NAME [--mask subject] Exposure2012=+0.2 Temperature=-150
    python3 -m engine.workflow learn [--run DIR]
    python3 -m engine.workflow calibrate

`match` copies the active photo's look onto the other selected photos and
solves each one's white balance / exposure / tone so it matches the active
photo. It writes report.json, contact_sheet_before.jpg (look pasted, nothing
solved) and contact_sheet.jpg (final) into the run folder.

`nudge` makes a small relative change to one photo from a run, re-renders it
and rebuilds the contact sheet. Claude Code uses it while reviewing.

`learn` and `calibrate` feed the self-learning (engine/learning.py); `match`
also learns automatically from every run.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from . import contact_sheet
from . import skin as skin_model
from .bridge import Bridge, BridgeError
from .guards import flags_for
from .learning import Learner, light_bucket
from .measure import Metrics, measure_file
from .settings import is_raw, split, starting_corrective
from .solver import CORRECTIVE, MIN_SKIN_FRACTION, Options, blend, clamp, match_error, propose, wb_limits

RUNS_DIR = Path.home() / ".matchlook" / "runs"
SNAPSHOT_NAME = "Before Match Look"
REVIEW_FLAGS = {"not_converged", "mostly_clipped", "low_neutral_confidence"}


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


def learn_from_run(bridge, run_dir, learner, log=print):
    """Compare a run's photos as they are in Lightroom now with what the match chose,
    and learn the differences (your edits after the match, and review nudges) as
    preferences. Each run is learned from once."""
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    if report.get("learned"):
        return 0
    try:
        current = {c["id"]: c["settings"] for c in bridge.get_settings([{"id": p["id"]} for p in report["photos"]])}
    except BridgeError as e:
        log(f"Couldn't read last run's photos to learn from them: {e}")
        return 0
    learned = 0
    for p in report["photos"]:
        settings = current.get(p["id"])
        if not settings or "matched" not in p:
            continue
        kept = {k: settings.get(k, p["final"].get(k, 0.0)) for k in CORRECTIVE}
        bucket = p.get("light") or light_bucket(p["matched"], p["is_raw"])
        if learner.observe_correction(p.get("camera"), p["is_raw"], bucket, p["matched"], kept,
                                      allow_zero=True) == "correction":
            learned += 1
    report["learned"] = True
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))
    learner.save()
    if learned:
        log(f"Learned from {learned} photo(s) adjusted after the last run ({run_dir.name})")
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
):
    """Match the selected photos to the active one. See the module docstring.

    skin: also keep skin tones consistent (engine/skin.py).
    color_only: solve white balance only; keep each photo's own exposure/tone.
    learner: engine.learning.Learner (self-learning); None disables learning.
    runs_dir: where earlier runs live, to learn from edits made since; default
        is out_dir's parent.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if learner is not None:
        previous = latest_unlearned_run(runs_dir or out_dir.parent, exclude=out_dir)
        if previous:
            learn_from_run(bridge, previous, learner, log)

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

    state = {}
    for t in targets:
        t_raw = is_raw(t)
        camera = t.get("cameraModel")
        start, wb_from_camera = starting_corrective(ref_corrective, ref_raw, t_raw, t.get("settings"))
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
        state[t["id"]] = {
            "photo": t, "is_raw": t_raw, "camera": camera, "start": start, "sliders": start,
            "wb_from_camera": wb_from_camera,
            "options": Options(prior=prior, skin=skin, color_only=color_only,
                               wb_limits=wb_limits(t_raw, wb_from_camera)),
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
            p = propose(ref_metrics, s["history"], is_raw=s["is_raw"], tolerance=tolerance,
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
        errors = [match_error(ref_metrics, h["metrics"], skin=o.skin, color_only=o.color_only) for h in s["history"]]
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

    for s in state.values():
        o = s["options"]
        s["final_error"] = match_error(ref_metrics, s["final_metrics"], skin=o.skin, color_only=o.color_only)
        s["flags"] = flags_for(s["final_metrics"], s["proposal"], tolerance)
        unscaled = strength == 1.0 and not s["learned_adjustment"]
        if unscaled and s["final_error"] >= tolerance and "not_converged" not in s["flags"]:
            s["flags"].append("not_converged")

    if learner is not None:
        for s in state.values():
            learner.observe_history(s["camera"], s["is_raw"], s["history"])
        learner.end_run()
        learner.save()

    ref_skin = _skin(ref_metrics)
    report = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strength": strength,
        "tolerance": tolerance,
        "size": size,
        "options": {"skin": skin, "color_only": color_only, "learning": learner is not None},
        "snapshot": None if snap.get("warning") else SNAPSHOT_NAME,
        "warnings": warnings,
        "reference": {"id": ref_id, "fileName": ref["fileName"], "preview": str(ref_path),
                      "metrics": ref_metrics.to_dict(), "skin": ref_skin},
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
            "start": s["start"],
            "matched": s["matched"],
            "final": s["final"],
            "learned_adjustment": s["learned_adjustment"],
            "start_error": round(s["start_error"], 2),
            "final_error": round(s["final_error"], 2),
            "iterations": s["proposal"].iterations,
            "flags": s["flags"] + s["extra_flags"],
            "skin": t_skin,
            "skin_vs_reference": skin_model.compare(ref_skin, t_skin) if ref_skin and t_skin else None,
            "start_preview": s["history"][0]["preview"],
            "preview": s["preview"],
        })
    _label_flagged(bridge, report, label)
    _write_outputs(out_dir, report)
    return report


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
    for p in report["photos"]:
        err = p["start_error"] if key == "start_preview" else p["final_error"]
        flags = [f for f in p["flags"] if key == "preview" or f not in REVIEW_FLAGS]
        notes = list(flags)
        if key == "preview" and p.get("skin_vs_reference") and p["skin_vs_reference"] != "matches the reference":
            notes.append(f"skin {p['skin_vs_reference']}")
        if key == "preview" and p.get("learned_adjustment"):
            notes.append("learned adj.")
        tiles.append({
            "path": p[key],
            "title": p["fileName"],
            "subtitle": f"error {err:.1f}" + (f" · {', '.join(notes)}" if notes else ""),
            "highlight": key == "preview" and bool(REVIEW_FLAGS & set(p["flags"])),
        })
    return tiles


def _write_outputs(out_dir, report):
    out_dir = Path(out_dir)
    contact_sheet.build(_tiles(report, "start_preview"), out_dir / "contact_sheet_before.jpg")
    contact_sheet.build(_tiles(report, "preview"), out_dir / "contact_sheet.jpg")
    report["contact_sheet"] = str(out_dir / "contact_sheet.jpg")
    report["contact_sheet_before"] = str(out_dir / "contact_sheet_before.jpg")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))


def parse_changes(pairs, allow_mask=False):
    """["Exposure2012=+0.2", "Tint=-3"] -> {"Exposure2012": 0.2, "Tint": -3.0}"""
    allowed = list(MASK_SLIDERS) if allow_mask else CORRECTIVE
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
                                         color_only=opts.get("color_only", False)), 2)
    p["skin"] = _skin(metrics)
    ref_skin = report["reference"].get("skin")
    p["skin_vs_reference"] = skin_model.compare(ref_skin, p["skin"]) if ref_skin and p["skin"] else None
    # A reviewed photo no longer needs the automatic "didn't converge" flag.
    p["flags"] = [f for f in p["flags"] if f != "not_converged"]


def run_nudge(bridge, run_dir, photo, changes, mask=None):
    """Apply relative slider changes to one photo from a run and refresh its preview.

    mask: None for the whole photo, or "subject" / "sky" / "background" /
    "people" to make the change only inside an AI mask (experimental; creates
    the mask the first time). The change is learned from at the next run.
    """
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    p = _find_photo(report, photo)
    warnings = report.setdefault("warnings", [])

    if mask is None:
        sliders = dict(p["final"])
        for key, delta in changes.items():
            if key not in CORRECTIVE:
                raise ValueError(f"{key} can only be changed inside a mask (--mask)")
            sliders[key] = sliders.get(key, 0.0) + delta
        sliders = clamp(sliders, p["is_raw"])
        _apply(bridge, [{"id": p["id"], "settings": _corrective_settings(sliders)}], warnings, lambda m: None)
        p["final"] = sliders
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

    _write_outputs(run_dir, report)
    return p


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
    learner.save()
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
    m.add_argument("--tolerance", type=float, default=2.0)
    m.add_argument("--max-iterations", type=int, default=6)
    m.add_argument("--size", type=int, default=1024, help="preview long edge in pixels")
    m.add_argument("--label", default="yellow", help="color label for photos that need review ('' for none)")
    m.add_argument("--out", help="run folder (default ~/.matchlook/runs/<time>)")
    n = sub.add_parser("nudge", help="relative slider changes to one photo from a run")
    n.add_argument("--run", required=True, help="run folder from `match`")
    n.add_argument("--photo", required=True, help="file name, file stem or id")
    n.add_argument("--mask", choices=MASK_KINDS, help="experimental: change only inside this AI mask")
    n.add_argument("changes", nargs="+", help="e.g. Exposure2012=+0.2 Temperature=-150 Tint=+3")
    lr = sub.add_parser("learn", help="learn now from edits made to a run's photos in Lightroom")
    lr.add_argument("--run", help="run folder (default: the latest one not learned from yet)")
    c = sub.add_parser("calibrate", help="measure Lightroom's slider response on the selected photos")
    c.add_argument("--out", help="folder for calibration renders")
    args = parser.parse_args(argv)

    bridge = Bridge()
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    try:
        if args.cmd == "match":
            if not 0.0 <= args.strength <= 1.0:
                parser.error("--strength must be between 0 and 1")
            out = Path(args.out) if args.out else RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
            learner = None if args.no_learning else Learner()
            report = run_match(bridge, out, strength=args.strength, tolerance=args.tolerance,
                               max_iterations=args.max_iterations, size=args.size, label=args.label,
                               skin=args.skin, color_only=args.color_only, learner=learner,
                               runs_dir=RUNS_DIR if not args.out else None, log=log)
            print(json.dumps({"run": str(out), **_summary(report)}, indent=2))
        elif args.cmd == "nudge":
            p = run_nudge(bridge, args.run, args.photo, parse_changes(args.changes, allow_mask=bool(args.mask)),
                          mask=args.mask)
            keys = ("fileName", "final", "masks", "final_error", "skin_vs_reference", "flags", "preview")
            print(json.dumps({k: p.get(k) for k in keys}, indent=2))
        elif args.cmd == "learn":
            learner = Learner()
            run = Path(args.run) if args.run else latest_unlearned_run(RUNS_DIR)
            if not run:
                print("Nothing new to learn from.")
            else:
                count = learn_from_run(bridge, run, learner, log)
                print(json.dumps({"run": str(run), "photos_learned_from": count, **learner.summary()}, indent=2))
        else:
            out = Path(args.out) if args.out else RUNS_DIR.parent / "calibration" / time.strftime("%Y%m%d-%H%M%S")
            print(json.dumps(run_calibrate(bridge, out, Learner(), log=log), indent=2))
    except (BridgeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _summary(report):
    return {
        "reference": report["reference"]["fileName"],
        "reference_skin": report["reference"].get("skin"),
        "contact_sheet": report["contact_sheet"],
        "contact_sheet_before": report["contact_sheet_before"],
        "warnings": report["warnings"],
        "photos": [
            {k: p.get(k) for k in ("fileName", "start_error", "final_error", "iterations", "flags", "final",
                                   "learned_adjustment", "skin_vs_reference")}
            for p in report["photos"]
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
