"""The full Match Look job, driven through the Lightroom bridge.

    python3 -m engine.workflow match [--strength 1.0] [--out DIR]
    python3 -m engine.workflow nudge --run DIR --photo NAME Exposure2012=+0.2 Temperature=-150

`match` copies the active photo's look onto the other selected photos and
solves each one's white balance / exposure / tone so it matches the active
photo. It writes report.json, contact_sheet_before.jpg (look pasted, nothing
solved) and contact_sheet.jpg (final) into the run folder.

`nudge` makes a small relative change to one photo from a run, re-renders it
and rebuilds the contact sheet. Claude Code uses it while reviewing.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from . import contact_sheet
from .bridge import Bridge, BridgeError
from .guards import flags_for
from .measure import Metrics, measure_file
from .settings import is_raw, split, starting_corrective
from .solver import CORRECTIVE, blend, clamp, match_error, propose

RUNS_DIR = Path.home() / ".matchlook" / "runs"
SNAPSHOT_NAME = "Before Match Look"
REVIEW_FLAGS = {"not_converged", "mostly_clipped", "low_neutral_confidence"}


def _corrective_settings(sliders):
    # "Custom" tells Lightroom to use our Temp/Tint rather than As Shot/Auto.
    return dict(sliders, WhiteBalance="Custom")


def run_match(
    bridge,
    out_dir,
    strength=1.0,
    tolerance=2.0,
    max_iterations=6,
    size=1024,
    label="yellow",
    log=print,
):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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

    snap = bridge.snapshot([{"id": t["id"], "name": SNAPSHOT_NAME} for t in targets]) or {}
    if snap.get("warning"):
        log(f"Warning: {snap['warning']}")

    state = {}
    for t in targets:
        t_raw = is_raw(t)
        start = starting_corrective(ref_corrective, ref_raw, t_raw)
        flags = []
        if t.get("cameraModel") != ref.get("cameraModel"):
            flags.append("different_camera")
        if t_raw != ref_raw:
            flags.append("different_file_type")
        state[t["id"]] = {"photo": t, "is_raw": t_raw, "start": start, "sliders": start,
                          "history": [], "done": False, "extra_flags": flags}

    bridge.apply_settings(
        [{"id": pid, "settings": {**creative, **_corrective_settings(s["start"])}} for pid, s in state.items()]
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
            metrics = measure_file(item["path"])
            s["history"].append({"sliders": s["sliders"], "metrics": metrics.to_dict(), "preview": item["path"]})
            p = propose(ref_metrics, s["history"], is_raw=s["is_raw"],
                        tolerance=tolerance, max_iterations=max_iterations)
            s["last_metrics"] = metrics
            s["proposal"] = p
            if p.done:
                s["done"] = True
            else:
                s["sliders"] = p.sliders
                updates.append({"id": item["id"], "settings": _corrective_settings(p.sliders)})
        log(f"Pass {iteration + 1}: {len(pending) - len(updates)} done, {len(updates)} still converging")
        if updates:
            bridge.apply_settings(updates)
        iteration += 1

    # Final values: best render found, scaled by strength.
    finals, rerender = [], []
    for pid, s in state.items():
        p = s["proposal"]
        errors = [match_error(ref_metrics, h["metrics"]) for h in s["history"]]
        best = min(range(len(errors)), key=errors.__getitem__)
        final = blend(s["start"], p.sliders, strength, s["is_raw"])
        s["final"] = final
        s["start_error"] = errors[0]
        finals.append({"id": pid, "settings": _corrective_settings(final)})
        if strength == 1.0 and best == len(errors) - 1:
            s["preview"] = s["history"][-1]["preview"]
            s["final_error"] = errors[-1]
            s["flags"] = flags_for(s["last_metrics"], p, tolerance)
        else:
            rerender.append(pid)
    bridge.apply_settings(finals)

    if rerender:
        final_dir = out_dir / "final"
        final_dir.mkdir(exist_ok=True)
        items = [{"id": pid, "path": str(final_dir / f"{_safe(state[pid]['photo'])}.jpg")} for pid in rerender]
        bridge.render(items, size=size)
        for item in items:
            s = state[item["id"]]
            metrics = measure_file(item["path"])
            s["preview"] = item["path"]
            s["final_error"] = match_error(ref_metrics, metrics)
            s["flags"] = flags_for(metrics, s["proposal"], tolerance)
            if strength == 1.0 and s["final_error"] >= tolerance and "not_converged" not in s["flags"]:
                s["flags"].append("not_converged")

    report = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strength": strength,
        "tolerance": tolerance,
        "size": size,
        "snapshot": None if snap.get("warning") else SNAPSHOT_NAME,
        "warnings": [snap["warning"]] if snap.get("warning") else [],
        "reference": {"id": ref_id, "fileName": ref["fileName"], "preview": str(ref_path),
                      "metrics": ref_metrics.to_dict()},
        "photos": [
            {
                "id": pid,
                "fileName": s["photo"]["fileName"],
                "is_raw": s["is_raw"],
                "start": s["start"],
                "final": s["final"],
                "start_error": round(s["start_error"], 2),
                "final_error": round(s["final_error"], 2),
                "iterations": s["proposal"].iterations,
                "flags": s["flags"] + s["extra_flags"],
                "start_preview": s["history"][0]["preview"],
                "preview": s["preview"],
            }
            for pid, s in state.items()
        ],
    }
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
        tiles.append({
            "path": p[key],
            "title": p["fileName"],
            "subtitle": f"error {err:.1f}" + (f" · {', '.join(flags)}" if flags else ""),
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


def parse_changes(pairs):
    """["Exposure2012=+0.2", "Tint=-3"] -> {"Exposure2012": 0.2, "Tint": -3.0}"""
    changes = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if key not in CORRECTIVE:
            raise ValueError(f"{key} is not one of {', '.join(CORRECTIVE)}")
        changes[key] = float(value)
    return changes


def run_nudge(bridge, run_dir, photo, changes, label="yellow"):
    """Apply relative slider changes to one photo from a run and refresh its preview."""
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text())
    matches = [p for p in report["photos"] if photo in (p["id"], p["fileName"], Path(p["fileName"]).stem)]
    if len(matches) != 1:
        raise ValueError(f"'{photo}' matches {len(matches)} photos in this run")
    p = matches[0]

    sliders = dict(p["final"])
    for key, delta in changes.items():
        sliders[key] = sliders.get(key, 0.0) + delta
    sliders = clamp(sliders, p["is_raw"])
    bridge.apply_settings([{"id": p["id"], "settings": _corrective_settings(sliders)}])

    nudge_dir = run_dir / "nudges"
    nudge_dir.mkdir(exist_ok=True)
    n = len(list(nudge_dir.glob("*.jpg")))
    path = nudge_dir / f"{n:02d}_{_safe(p)}.jpg"
    bridge.render([{"id": p["id"], "path": str(path)}], size=report.get("size", 1024))

    ref_metrics = Metrics.from_dict(report["reference"]["metrics"])
    metrics = measure_file(path)
    p["final"] = sliders
    p["preview"] = str(path)
    p["final_error"] = round(match_error(ref_metrics, metrics), 2)
    p.setdefault("nudges", []).append(changes)
    # A reviewed photo no longer needs the automatic "didn't converge" flag.
    p["flags"] = [f for f in p["flags"] if f != "not_converged"]
    _write_outputs(run_dir, report)
    return p


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match the look of the active Lightroom photo across the selection.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="match the selected photos to the active one")
    m.add_argument("--strength", type=float, default=1.0, help="0..1, how much of the correction to apply")
    m.add_argument("--tolerance", type=float, default=2.0)
    m.add_argument("--max-iterations", type=int, default=6)
    m.add_argument("--size", type=int, default=1024, help="preview long edge in pixels")
    m.add_argument("--label", default="yellow", help="color label for photos that need review ('' for none)")
    m.add_argument("--out", help="run folder (default ~/.matchlook/runs/<time>)")
    n = sub.add_parser("nudge", help="relative slider changes to one photo from a run")
    n.add_argument("--run", required=True, help="run folder from `match`")
    n.add_argument("--photo", required=True, help="file name, file stem or id")
    n.add_argument("changes", nargs="+", help="e.g. Exposure2012=+0.2 Temperature=-150 Tint=+3")
    args = parser.parse_args(argv)

    bridge = Bridge()
    try:
        if args.cmd == "match":
            if not 0.0 <= args.strength <= 1.0:
                parser.error("--strength must be between 0 and 1")
            out = Path(args.out) if args.out else RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")
            report = run_match(bridge, out, strength=args.strength, tolerance=args.tolerance,
                               max_iterations=args.max_iterations, size=args.size, label=args.label,
                               log=lambda msg: print(msg, file=sys.stderr))
            print(json.dumps({"run": str(out), **_summary(report)}, indent=2))
        else:
            p = run_nudge(bridge, args.run, args.photo, parse_changes(args.changes))
            print(json.dumps({k: p[k] for k in ("fileName", "final", "final_error", "flags", "preview")}, indent=2))
    except (BridgeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _summary(report):
    return {
        "reference": report["reference"]["fileName"],
        "contact_sheet": report["contact_sheet"],
        "contact_sheet_before": report["contact_sheet_before"],
        "warnings": report["warnings"],
        "photos": [
            {k: p[k] for k in ("fileName", "start_error", "final_error", "iterations", "flags", "final")}
            for p in report["photos"]
        ],
    }


if __name__ == "__main__":
    sys.exit(main())
